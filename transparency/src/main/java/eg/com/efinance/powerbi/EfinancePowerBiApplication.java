package eg.com.efinance.powerbi;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class EfinancePowerBiApplication {

    public static void main(String[] args) {
        SpringApplication.run(EfinancePowerBiApplication.class, args);
    }
}
